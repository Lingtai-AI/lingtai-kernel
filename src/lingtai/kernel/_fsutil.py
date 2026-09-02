"""Shared low-level filesystem / JSON / JSONL helpers for kernel state writes.

This module is dependency-light (stdlib only) on purpose: it sits *below* the
rest of the kernel so any module can import it without creating cycles.  It
centralises the small set of I/O decisions that were previously re-solved,
slightly differently, in dozens of call sites:

- crash-atomic replace via a temp file in the *same directory* + ``os.replace``
  (atomic only on the same filesystem, so the temp must be a sibling of the
  target, never ``/tmp``),
- one UTF-8 / ``ensure_ascii`` policy for model-visible JSON,
- a single ``read_json(default=...)`` exception policy,
- append-only JSONL with a returned byte offset for callers that index records.

The helpers intentionally match the *existing* dominant behaviour so callers
can migrate without changing public file formats:

- ``atomic_write_json`` writes ``json.dumps(obj, ensure_ascii=False, indent=2)``
  with **no** trailing newline (matches ``Workdir.write_manifest``).
- ``fsync`` is **opt-in** (default off) so migrating a non-fsync caller does not
  silently change durability behaviour.
- ``append_jsonl`` defaults to ``ensure_ascii=True`` to match the token ledger
  and other ASCII-escaped JSONL logs; pass ``ensure_ascii=False`` for
  UTF-8-preserving logs.

See ``docs/plans/2026-06-25-fsutil-migration.md`` for the issue #510 plan.
"""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Union

__all__ = [
    "atomic_write_text",
    "atomic_write_json",
    "JSONNumber",
    "read_json",
    "append_jsonl",
    "iter_jsonl_records",
    "tail_jsonl_records",
    "utc_now_iso",
]

PathLike = Union[str, "os.PathLike[str]"]

# Sentinel so ``read_json(path)`` can distinguish "no default given" (raise on
# error) from ``read_json(path, default=None)`` (return None on error).
_NO_DEFAULT = object()

# RFC 8259 JSON number grammar.  A string subclass lets a decoder retain the
# exact lexeme (like Go's json.Number) while callers can still compare it as a
# JSON scalar.
_JSON_NUMBER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


class JSONNumber(str):
    """A validated raw JSON number token for lossless JSON read-modify-write.

    ``json.loads(..., parse_int=JSONNumber, parse_float=JSONNumber)`` retains
    the input token rather than converting it to an int or float.  Pass an
    object containing these values to :func:`atomic_write_json` with
    ``preserve_number_tokens=True`` to emit them as JSON numbers, not strings.
    """

    def __new__(cls, value: str) -> "JSONNumber":
        if not isinstance(value, str) or not _JSON_NUMBER_RE.fullmatch(value):
            raise ValueError(f"invalid JSON number token: {value!r}")
        return super().__new__(cls, value)


def _unique_tmp(target: Path) -> Path:
    """Return a unique sibling temp path for ``target`` (same dir → atomic replace).

    The name embeds both the pid *and* a random uuid4 hex so two writers to the
    same target never share a temp path — including threads/tasks inside one
    process, which a pid-only suffix could not distinguish (the audit flagged
    pid-only/fixed temp names as a same-process collision risk: two writers
    would race on one temp file, so one could ``os.replace`` it out from under
    the other and the loser fails with ``FileNotFoundError`` or, worse, writes
    into an inode already renamed onto the target).

    A uuid4 sibling (rather than ``tempfile.mkstemp``) is used deliberately so
    the temp file is created by ``open(..., "x")`` and inherits the process
    umask, preserving the permission semantics of the plain-``open`` atomic
    writes these helpers replace. ``mkstemp`` forces mode ``0o600``, which
    ``os.replace`` would then carry onto the target, silently tightening the
    permissions of migrated state files.
    """
    return target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")


def atomic_write_text(
    path: PathLike,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync: bool = False,
    preserve_existing_mode: bool = False,
) -> Path:
    """Atomically write ``text`` to ``path``.

    Writes to a sibling temp file then ``os.replace``s it over the target, so a
    crash mid-write leaves either the old file or the new one, never a partial.
    The parent directory is created if missing.

    ``fsync`` is opt-in: when True the temp file's bytes are flushed to disk
    before the rename (stronger crash durability, extra I/O cost).  Leave it
    off to preserve the behaviour of callers that never fsynced.  Note this
    fsyncs the *file content* only, not the parent directory, so the rename
    metadata is not guaranteed durable across a power loss; that is stronger
    than the default and sufficient for the current opt-out callers.

    ``preserve_existing_mode`` is also opt-in.  When True and the target
    exists, its permission bits are applied to the new temp file before the
    replace.  A missing target still inherits the process umask, and the
    default False preserves the existing behaviour for other callers.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp(target)
    try:
        existing_mode = None
        if preserve_existing_mode:
            try:
                existing_mode = stat.S_IMODE(target.stat().st_mode)
            except FileNotFoundError:
                pass
        # "x" (exclusive create) guarantees we never write into a temp file
        # another writer already created; combined with the uuid4 name this
        # makes concurrent same-target writes collision-free.
        with open(tmp, "x", encoding=encoding) as f:
            f.write(text)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        if existing_mode is not None:
            tmp.chmod(existing_mode)
        os.replace(str(tmp), str(target))
    except BaseException:
        # Best-effort cleanup so a failed write does not leave temp litter.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target


def _contains_string(value: Any, candidate: str) -> bool:
    """Return whether *candidate* occurs as a string anywhere in JSON-like data."""
    if isinstance(value, str):
        return value == candidate
    if isinstance(value, dict):
        return any(
            _contains_string(key, candidate) or _contains_string(item, candidate)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_string(item, candidate) for item in value)
    return False


def _json_dumps_preserving_number_tokens(
    obj: Any,
    *,
    ensure_ascii: bool,
    indent: Optional[int],
    sort_keys: bool,
    default: Optional[Callable[[Any], Any]],
) -> str:
    """Serialize JSONNumber values as validated raw JSON numbers.

    The stdlib encoder correctly handles all ordinary values and formatting, but
    treats a ``str`` subclass as a JSON string.  Substitute collision-free
    markers for only the raw-number leaves, run the standard encoder with
    ``allow_nan=False``, then replace each complete encoded marker with its
    already-validated numeric token.  The strict mode guarantees a caller cannot
    produce non-standard ``NaN``/``Infinity`` output while preserving values
    decoded with ``parse_int``/``parse_float`` exactly.
    """
    markers: dict[str, str] = {}

    def replace_numbers(value: Any) -> Any:
        if isinstance(value, JSONNumber):
            marker = f"__lingtai_json_number_{uuid.uuid4().hex}__"
            while _contains_string(obj, marker) or marker in markers:
                marker = f"__lingtai_json_number_{uuid.uuid4().hex}__"
            markers[marker] = str(value)
            return marker
        if isinstance(value, dict):
            return {key: replace_numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_numbers(item) for item in value]
        if isinstance(value, tuple):
            return tuple(replace_numbers(item) for item in value)
        return value

    text = json.dumps(
        replace_numbers(obj),
        ensure_ascii=ensure_ascii,
        indent=indent,
        sort_keys=sort_keys,
        default=default,
        allow_nan=False,
    )
    for marker, token in markers.items():
        text = text.replace(json.dumps(marker, ensure_ascii=ensure_ascii), token)
    return text


def atomic_write_json(
    path: PathLike,
    obj: Any,
    *,
    ensure_ascii: bool = False,
    indent: Optional[int] = 2,
    sort_keys: bool = False,
    default: Optional[Callable[[Any], Any]] = None,
    fsync: bool = False,
    preserve_existing_mode: bool = False,
    preserve_number_tokens: bool = False,
) -> Path:
    """Atomically write ``obj`` as JSON to ``path``.

    Defaults (``ensure_ascii=False``, ``indent=2``, no trailing newline) match
    the kernel's dominant model-visible JSON convention.  Serialization happens
    *before* the file is touched, so a non-serializable ``obj`` raises without
    leaving a temp file or clobbering the target.

    ``preserve_existing_mode`` is forwarded to :func:`atomic_write_text` and
    remains disabled by default.  ``preserve_number_tokens`` is a strict,
    opt-in read-modify-write mode for :class:`JSONNumber`: it emits those
    validated lexemes as numbers and rejects non-standard float constants.  The
    default remains the ordinary ``json.dumps`` path for all existing callers.
    """
    if preserve_number_tokens:
        text = _json_dumps_preserving_number_tokens(
            obj,
            ensure_ascii=ensure_ascii,
            indent=indent,
            sort_keys=sort_keys,
            default=default,
        )
    else:
        text = json.dumps(
            obj,
            ensure_ascii=ensure_ascii,
            indent=indent,
            sort_keys=sort_keys,
            default=default,
        )
    return atomic_write_text(
        path,
        text,
        encoding="utf-8",
        fsync=fsync,
        preserve_existing_mode=preserve_existing_mode,
    )


def read_json(
    path: PathLike,
    *,
    default: Any = _NO_DEFAULT,
    expect: Optional[Union[type, tuple]] = None,
) -> Any:
    """Read JSON from ``path`` with one consistent exception policy.

    Returns the parsed object.  On a missing file, unreadable file, malformed
    JSON, or (when ``expect`` is given) a wrong top-level type:

    - if ``default`` was supplied, return ``default``;
    - otherwise re-raise the underlying error (``FileNotFoundError``,
      ``json.JSONDecodeError``, ``OSError``) or a ``TypeError`` for ``expect``.

    ``expect`` is an optional type or tuple of types the top-level value must be
    an instance of (e.g. ``dict`` for a manifest, ``list`` for an array file).
    """
    target = Path(path)
    try:
        obj = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        if default is not _NO_DEFAULT:
            return default
        raise
    if expect is not None and not isinstance(obj, expect):
        if default is not _NO_DEFAULT:
            return default
        raise TypeError(
            f"{target}: expected top-level {expect}, got {type(obj).__name__}"
        )
    return obj


def append_jsonl(
    path: PathLike,
    obj: Any,
    *,
    ensure_ascii: bool = True,
    default: Optional[Callable[[Any], Any]] = None,
    fsync: bool = False,
) -> int:
    """Append ``obj`` as a single JSONL record and return its byte offset.

    The returned offset is the position of the record's first byte (``f.tell()``
    before the write), matching the token ledger's ``source_offset`` contract so
    callers can index into the file later.  The parent directory is created if
    missing.  ``ensure_ascii`` defaults to True to match existing ASCII-escaped
    ledgers; pass False for UTF-8-preserving logs.

    Concurrency: the returned offset is durable only under a single writer (or
    an externally held lock). The ``O_APPEND`` write itself is atomic, but a
    second writer can append between this call's ``tell()`` and ``write``, so
    the offset is reliable record provenance only when one writer owns the file
    (matching the existing token-ledger pattern, which serializes via an
    in-process lock at the call site).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(obj, ensure_ascii=ensure_ascii, default=default) + "\n").encode(
        "utf-8"
    )
    with open(target, "ab") as f:
        offset = f.tell()
        f.write(payload)
        f.flush()
        if fsync:
            os.fsync(f.fileno())
    return offset


def iter_jsonl_records(
    path: PathLike,
    *,
    skip_invalid: bool = True,
) -> Iterator[Any]:
    """Yield parsed records from a JSONL file in file order.

    A missing file yields nothing.  Blank lines are skipped.  When
    ``skip_invalid`` is True (default) malformed lines are skipped silently,
    matching log-recovery paths that must tolerate a torn final write; pass
    False to surface ``json.JSONDecodeError``.
    """
    target = Path(path)
    try:
        handle = open(target, "r", encoding="utf-8")
    except FileNotFoundError:
        return
    with handle as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                if skip_invalid:
                    continue
                raise


def tail_jsonl_records(
    path: PathLike,
    n: int,
    *,
    skip_invalid: bool = True,
) -> list:
    """Return the last ``n`` parsed records from a JSONL file, in file order.

    Convenience reverse-tail for recovery paths that only need the most recent
    entries.  Reads the whole file (callers with very large ledgers should use a
    seek-based tail); kept simple and dependency-light here.
    """
    if n <= 0:
        return []
    records = list(iter_jsonl_records(path, skip_invalid=skip_invalid))
    return records[-n:]


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    One canonical timestamp representation (timezone-aware UTC), matching the
    dominant ``datetime.now(timezone.utc).isoformat()`` usage across the kernel.
    """
    return datetime.now(timezone.utc).isoformat()
