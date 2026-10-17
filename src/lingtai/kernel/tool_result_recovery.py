"""Recover durable tool results for pending-call heal paths.

The event log may contain a real ``tool_result`` for a tool call whose
model-visible ``ToolResultBlock`` was later rolled back by a failed LLM
continuation.  Before synthesizing an abort placeholder, heal paths can ask
this module to replay that already-executed result from ``logs/events.jsonl``.

Newly finalized executed-tool blocks append a separate
``tool_result_projection`` event with exact kernel finalization provenance.
The bounded reverse scan restores its canonical content, metadata and frozen
model snapshot without recomputing policy. It never replaces the original raw
``tool_result`` event used by raw locators. Missing extensions keep legacy replay.

Besides the canonical executed-tool record, a kernel-internal synthetic
recovery record (``BaseAgent._inject_notification_pair``) may carry the
extension fields ``result_metadata`` / ``synthesized`` / ``redacted``.  They
are honored only for that exact synthetic provenance or the finalized event
above. Other records — even with lookalike fields — recover with the defaults
(``metadata={}``, ``synthesized=False``, no frozen model snapshot).
A ``redacted: true`` replay is lossy by design: the rebuilt block gets a
``metadata["redacted"] = True`` marker so callers trigger producer
reconciliation (LICC contract "Synthetic pair durability and heal replay").
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .llm.interface import ToolResultBlock
from .tool_result_artifacts import PREVENTIVE_MAX_CHARS, spill_oversized_result


DEFAULT_MAX_SCAN_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_SCAN_EVENTS = 10_000
_REVERSE_READ_CHUNK = 64 * 1024

LoggerFn = Callable[..., None]


@dataclass
class _ScanStats:
    scanned_events: int = 0
    scanned_bytes: int = 0


@dataclass(frozen=True)
class _RecoveredEvent:
    result: Any
    event_ts: Any
    event_tool_name: str | None
    stats: _ScanStats
    # Internal recovery extension fields; populated only for exact synthetic
    # or finalized-block provenance (see module docstring).
    result_metadata: dict = field(default_factory=dict)
    model_projection: dict | None = None
    synthesized: bool = False
    redacted: bool = False


def recover_tool_result_block_from_events(
    working_dir: Path | str,
    *,
    tool_call_id: str,
    tool_name: str,
    logger_fn: LoggerFn | None = None,
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
    max_scan_events: int = DEFAULT_MAX_SCAN_EVENTS,
    max_result_chars: int = PREVENTIVE_MAX_CHARS,
) -> ToolResultBlock | None:
    """Return a real replayed result block, or ``None`` for synthetic fallback.

    Matching is deliberately strict:
    - event is ``tool_result`` or a trusted finalized ``tool_result_projection``;
    - event ``tool_call_id`` must exactly equal the pending call id;
    - when the event carries ``tool_name``, it must exactly equal the pending
      tool name.

    The scan is tail-first and bounded.  The newest matching event wins.
    All failures are swallowed and surfaced only through safe metadata logs.
    """
    try:
        recovered = _find_latest_tool_result_event(
            Path(working_dir) / "logs" / "events.jsonl",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            max_scan_bytes=max_scan_bytes,
            max_scan_events=max_scan_events,
        )
    except Exception as exc:  # noqa: BLE001 - recovery must never break heal
        _safe_log(
            logger_fn,
            "tool_result_replay_failed",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase="scan",
            error=f"{type(exc).__name__}: {exc}"[:200],
        )
        return None

    if recovered is None:
        _safe_log(
            logger_fn,
            "tool_result_replay_miss",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            reason="no_matching_tool_result",
        )
        return None

    try:
        capped = spill_oversized_result(
            recovered.result,
            max_chars=max_result_chars,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            working_dir=working_dir,
            source="recovered_from_events",
        )
    except Exception as exc:  # noqa: BLE001 - do not risk raw oversized replay
        _safe_log(
            logger_fn,
            "tool_result_replay_failed",
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase="spill",
            error=f"{type(exc).__name__}: {exc}"[:200],
            scanned_events=recovered.stats.scanned_events,
            scanned_bytes=recovered.stats.scanned_bytes,
        )
        return None

    spill_fields: dict[str, Any] = {}
    if capped is not recovered.result and isinstance(capped, dict):
        spill_fields = {
            "spill_path": capped.get("spill_path"),
            "original_char_count": capped.get("original_char_count"),
        }

    _safe_log(
        logger_fn,
        "tool_result_replayed_from_log",
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        event_tool_name=recovered.event_tool_name,
        event_ts=_bounded_scalar(recovered.event_ts),
        result_type=type(capped).__name__,
        spilled=capped is not recovered.result,
        recovered_synthesized=recovered.synthesized,
        recovered_redacted=recovered.redacted,
        scanned_events=recovered.stats.scanned_events,
        scanned_bytes=recovered.stats.scanned_bytes,
        **spill_fields,
    )
    metadata = (
        copy.deepcopy(recovered.result_metadata)
        if recovered.result_metadata
        else {}
    )
    if recovered.redacted:
        # Non-equivalence marker: the durable record holds only the redacted
        # projection; _meta.redacted lets callers trigger reconciliation.
        metadata["redacted"] = True
    return ToolResultBlock(
        id=tool_call_id,
        name=tool_name,
        content=capped,
        metadata=metadata,
        synthesized=recovered.synthesized,
        model_projection=(
            copy.deepcopy(recovered.model_projection)
            if capped is recovered.result else None
        ),
    )


def _find_latest_tool_result_event(
    events_path: Path,
    *,
    tool_call_id: str,
    tool_name: str,
    max_scan_bytes: int,
    max_scan_events: int,
) -> _RecoveredEvent | None:
    if not tool_call_id or not events_path.is_file():
        return None

    stats = _ScanStats()
    for line in _iter_jsonl_lines_reverse(
        events_path,
        max_bytes=max_scan_bytes,
        stats=stats,
    ):
        if max_scan_events > 0 and stats.scanned_events >= max_scan_events:
            break
        stats.scanned_events += 1

        # Cheap prefilter before json.loads; IDs are provider-generated ASCII in
        # practice, but correctness still comes from the exact field checks below.
        if tool_call_id not in line or "tool_result" not in line:
            continue
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type", event.get("event"))
        finalized = (
            event_type == "tool_result_projection"
            and event.get("origin") == "kernel_tool_result_finalization"
        )
        if event_type != "tool_result" and not finalized:
            continue
        if event.get("tool_call_id") != tool_call_id:
            continue
        if "tool_name" in event and event.get("tool_name") is not None:
            if event.get("tool_name") != tool_name:
                continue
        if "result" not in event:
            continue
        # Extension fields trusted ONLY under the exact kernel provenance
        # discriminator; lookalike fields elsewhere keep the defaults.
        result_metadata: dict = {}
        model_projection = None
        synthesized = False
        redacted = False
        if finalized or (
            event.get("origin") == "kernel_notification_sync"
            and event.get("tool_name") == "notification"
        ):
            raw_metadata = event.get("result_metadata")
            if isinstance(raw_metadata, dict):
                result_metadata = raw_metadata
            snapshot = event.get("model_projection")
            if isinstance(snapshot, dict) and set(snapshot) == {"value"}:
                model_projection = snapshot
            elif finalized:
                # A malformed extension must not shadow the original raw event.
                continue
            synthesized = event.get("synthesized") is True
            redacted = event.get("redacted") is True
        return _RecoveredEvent(
            result=event.get("result"),
            event_ts=event.get("ts"),
            event_tool_name=event.get("tool_name"),
            stats=stats,
            result_metadata=result_metadata,
            model_projection=model_projection,
            synthesized=synthesized,
            redacted=redacted,
        )
    return None


def _iter_jsonl_lines_reverse(
    path: Path,
    *,
    max_bytes: int,
    stats: _ScanStats,
):
    """Yield non-empty JSONL lines tail-first, reading at most ``max_bytes``."""
    if max_bytes <= 0:
        return
    with path.open("rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        carry = b""
        while pos > 0 and stats.scanned_bytes < max_bytes:
            remaining_budget = max_bytes - stats.scanned_bytes
            read_size = min(_REVERSE_READ_CHUNK, pos, remaining_budget)
            if read_size <= 0:
                break
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size) + carry
            stats.scanned_bytes += read_size
            lines = chunk.split(b"\n")
            if pos > 0:
                carry = lines[0]
                tail = lines[1:]
            else:
                carry = b""
                tail = lines
            for raw in reversed(tail):
                if not raw:
                    continue
                try:
                    yield raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue


def _bounded_scalar(value: Any, *, limit: int = 120) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if len(text) > limit:
        return text[:limit]
    return text


def _safe_log(logger_fn: LoggerFn | None, event_type: str, **fields: Any) -> None:
    if logger_fn is None:
        return
    try:
        logger_fn(event_type, **fields)
    except Exception:
        pass
