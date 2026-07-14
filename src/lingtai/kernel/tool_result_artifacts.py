"""Tool-result artifact store — preventive spill of oversized fresh results.

``spill_oversized_result`` is called by ``ToolExecutor`` on every
newly-built tool result, before it ever reaches canonical history or a
provider wire.  If serialized content exceeds ``PREVENTIVE_MAX_CHARS``
(200_000 hard ceiling), the full original is written to
``<workdir>/tmp/tool-results/<…>.{json,txt}`` and a compact manifest dict
(``status="spilled"``, ``artifact="lingtai_tool_result_spill"``) replaces
the wire-bound content of that fresh result. This is a first-construction
decision, not a rebuild/replay mutation: the manifest IS the canonical
content from the moment the result is built, so no historical holder is
ever rewritten by this module.

This module previously also retroactively rewrote ``ToolResultBlock.content``
already committed to canonical history (via a since-removed
``compact_oversized_history``, invoked by the AED retry path in
``base_agent/turn.py``). That mutated historical tool-result bodies without
an explicit ``summarize`` replacement, which the provider-context replay
invariant forbids — only ``lingtai.tools.system.summarize`` may replace a
historical tool-result body. An unresolved over-window AED error now fails
loud (deterministically exhausts AED and falls through to the existing
preset-fallback / ASLEEP path) instead of being silently shrunk; see
``base_agent/turn.py::_is_over_window_error``.

This module also previously exposed a since-removed ``mark_expired_spill_
manifests``, called from ``base_agent/lifecycle.py::_start`` on every
restore to rewrite historical spill-manifest fields (``artifact_state``,
``artifact_expired_at``, ``warning``, and a legacy ``artifact_lifetime``
backfill) in ``history/chat_history.jsonl`` based on CURRENT sidecar
file-existence. That was the same category of violation as
``compact_oversized_history`` above: a startup/restore-conditioned rewrite
of already-committed ``ToolResultBlock.content`` with no explicit
``summarize`` replacement. Restored/persisted spill manifests are recorded
exactly as they were written; current sidecar availability is a read-time
concern for a non-provider-facing surface (UI/log/access-time diagnostic),
not something the kernel silently back-writes into provider-visible history.
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import re as _re
import uuid as _uuid
from pathlib import Path
from typing import Any

from .workdir import workdir_layout
from ._fsutil import atomic_write_text

# Stable, namespaced literal stamped into every manifest produced by this
# module.  Detectors require it for new manifests; older manifests that
# pre-date this field are still accepted by ``is_spill_manifest`` via the
# legacy-shape branch so persisted history from earlier turns stays
# readable.
ARTIFACT_MARKER = "lingtai_tool_result_spill"

# Top-level reserved fields that ``ToolExecutor`` attaches to dict-shaped
# primary results before they reach the wire.  When the primary result
# itself is oversized and gets spilled, replacing the whole dict with the
# manifest would silently drop provider-visible advisory metadata.  The
# advisory payload is small by construction.
#
# Deliberately tight allowlist.  Arbitrary business-level top-level keys
# (e.g. a tool returning ``{"data": [...]}``) are NOT hoisted — that's
# what the artifact file is for.  ``_meta`` is also intentionally
# omitted: it's stamped by ``stamp_meta`` and a copy lives in the
# artifact; agents that want timing/context can read the sidecar.
_HOISTED_RESERVED_FIELDS = ("_advisory",)

# Preventive cap — applied by ToolExecutor on every freshly built tool
# result, before it reaches the LLM wire. This is the non-configurable hard
# ceiling for provider-visible tool results.
PREVENTIVE_MAX_CHARS = 200_000

# Filename slugging — keep tool/call-id readable but filesystem-safe.
_FILENAME_SAFE_RE = _re.compile(r"[^A-Za-z0-9_.-]+")


def _serialized_len(value: Any) -> int:
    """Return the JSON-serialized character length used for the cap check."""
    if isinstance(value, str):
        return len(value)
    try:
        return len(_json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def _slug(value: str, *, limit: int = 40) -> str:
    cleaned = _FILENAME_SAFE_RE.sub("_", value).strip("_") or "tool"
    return cleaned[:limit]


def is_spill_manifest(value: Any) -> bool:
    """Return True iff ``value`` is a manifest produced by ``spill_oversized_result``.

    Detection is conservative.  The preferred shape carries the explicit
    namespaced marker ``artifact == ARTIFACT_MARKER`` *and* the required
    structural fields (``status="spilled"``, ``spill_path`` key,
    ``cap_chars``, ``original_char_count``) — this matches everything
    produced by the current implementation and refuses arbitrary business
    dicts that happen to use ``status`` + ``spill_path`` independently.

    Backward-compatible legacy branch: dicts without the marker are still
    accepted as manifests when *all four* structural fields are present
    with the right types.  This preserves recognition of any persisted
    history from earlier turns of this same patch (which produced manifests
    before the marker was added) but rejects unrelated dicts that happen to
    share one or two keys.
    """
    if not isinstance(value, dict):
        return False
    if value.get("status") != "spilled":
        return False
    if "spill_path" not in value:
        return False
    if value.get("artifact") == ARTIFACT_MARKER:
        return True
    # Legacy-shape fallback: require the full structural quadruple.
    return (
        "cap_chars" in value
        and "original_char_count" in value
        and isinstance(value.get("cap_chars"), int)
        and isinstance(value.get("original_char_count"), int)
    )


def spill_oversized_result(
    result: Any,
    *,
    max_chars: int,
    tool_name: str | None,
    tool_call_id: str | None,
    working_dir: Path | str | None,
    source: str = "preventive",
) -> Any:
    """Spill a too-large tool result to a sidecar file; return a compact manifest.

    If ``result`` already is a spill manifest, returns it unchanged (the
    history may have been compacted in a previous pass).  If the serialized
    length is ``<= max_chars``, returns ``result`` unchanged.

    Otherwise writes the *full* canonical serialization to
    ``<working_dir>/tmp/tool-results/<timestamp>-<tool>-<id>-<uuid>.<ext>``
    and returns a small dict containing a warning, the artifact paths (both
    workdir-relative and absolute), original size, cap, tool/call metadata,
    a UTC timestamp, a short preview, and a ``source`` field marking which
    code path produced the spill (for example ``"preventive"``,
    ``"retroactive"``, or ``"recovered_from_events"``).

    When the original is a dict, reserved provider-visible fields listed in
    ``_HOISTED_RESERVED_FIELDS`` (currently ``_advisory``) are
    copied verbatim from the original onto the manifest so they survive the
    wire replacement.  The artifact file always holds the complete
    post-dispatch original, including those fields, so nothing is lost — the
    hoist only makes advisory metadata visible on the wire-bound copy.

    When ``working_dir`` is None or the write fails, returns the manifest
    with ``spill_path`` / ``spill_path_abs`` set to None and a
    ``spill_error`` field — the wire is still safe (compact and capped),
    but the full content is unreachable.  Callers must provide a writable
    workdir to guarantee the "artifact contains the full original"
    invariant.
    """
    if max_chars <= 0:
        return result
    if is_spill_manifest(result):
        return result

    original_chars = _serialized_len(result)
    if original_chars <= max_chars:
        return result

    # Compute byte size (UTF-8) of the canonical serialization for the manifest.
    if isinstance(result, str):
        serialized_text = result
    else:
        try:
            serialized_text = _json.dumps(result, ensure_ascii=False, default=str, indent=2)
        except (TypeError, ValueError):
            serialized_text = str(result)
    original_bytes = len(serialized_text.encode("utf-8"))

    timestamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
    iso_timestamp = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    tool_slug = _slug(tool_name or "tool")
    id_slug = _slug(tool_call_id) if tool_call_id else _uuid.uuid4().hex[:8]
    ext = "txt" if isinstance(result, str) else "json"
    # Append a short uuid to defuse intra-second collisions across parallel
    # calls that share or lack a tool_call_id.
    unique = _uuid.uuid4().hex[:6]
    filename = f"{timestamp}-{tool_slug}-{id_slug}-{unique}.{ext}"

    spill_path_str: str | None = None
    spill_path_abs: str | None = None
    spill_failed: str | None = None
    if working_dir is not None:
        wd = Path(working_dir)
        spill_dir = workdir_layout(wd).tool_results_dir
        try:
            spill_dir.mkdir(parents=True, exist_ok=True)
            spill_path = spill_dir / filename
            atomic_write_text(spill_path, serialized_text)
            spill_path_str = str(spill_path.relative_to(wd))
            spill_path_abs = str(spill_path.resolve())
        except OSError as exc:
            spill_failed = f"{type(exc).__name__}: {exc}"

    # Build the compact manifest.  Preview is a head of the canonical text,
    # bounded so the manifest itself stays comfortably under the cap even
    # after `stamp_meta` adds ~200-400 chars.
    preview_budget = max(0, max_chars - 1500)
    preview_budget = min(preview_budget, 2000)
    preview = serialized_text[:preview_budget]
    if len(serialized_text) > preview_budget:
        preview += f"\n[... {len(serialized_text) - preview_budget} more chars in artifact ...]"

    warning = (
        f"Tool result was too large ({original_chars} chars, cap {max_chars}) "
        "and was written to a sidecar file under tmp/tool-results/ which is "
        "ephemeral and may be cleaned up. If the file is missing, the full "
        "content is gone — use the preview below or rerun the source tool."
    )

    manifest: dict[str, Any] = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "source": source,
        "warning": warning,
        "spill_path": spill_path_str,
        "spill_path_abs": spill_path_abs,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "original_char_count": original_chars,
        "original_byte_count": original_bytes,
        "cap_chars": max_chars,
        "timestamp": iso_timestamp,
        "preview": preview,
        "artifact_lifetime": "ephemeral_tmp",
        "artifact_state": "available",
    }
    if spill_failed is not None:
        manifest["spill_error"] = spill_failed
    if working_dir is None:
        manifest["spill_error"] = manifest.get("spill_error") or "no working_dir configured"

    # When the sidecar could not be written (no working_dir or write
    # failure), the full content is unreachable — mark as unavailable.
    if spill_path_str is None:
        manifest["artifact_state"] = "unavailable"

    # Hoist a small allowlist of provider-visible reserved fields from a
    # dict-shaped original onto the manifest, so loop-guard duplicate
    # warnings reach the wire even when the primary payload was too large to
    # inline.  The allowlist is deliberately tight
    # (`_HOISTED_RESERVED_FIELDS`); arbitrary business keys live in the
    # artifact only.  Hoisting runs BEFORE the defensive trim loop so the
    # preview-trim accounts for the hoisted bytes.
    if isinstance(result, dict):
        for key in _HOISTED_RESERVED_FIELDS:
            if key in result:
                manifest[key] = result[key]

    # Defensive: if the manifest itself somehow exceeds the cap (e.g. an
    # absurd tool_call_id or unexpectedly large reserved warning), trim the
    # preview further.  Loop is bounded; this is defence-in-depth, not a
    # routine code path.
    for _ in range(4):
        if _serialized_len(manifest) <= max_chars:
            break
        preview = manifest["preview"]
        if not preview:
            break
        manifest["preview"] = preview[: max(0, len(preview) // 2)]
    return manifest
