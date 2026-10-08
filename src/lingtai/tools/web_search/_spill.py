"""Shared Web inline-vs-artifact delivery decision for ``search`` and ``browse``.

Both actions build their complete canonical content first (the full rendered
result set for search; the full extracted document for browse), then call
:func:`spill_if_over_threshold` once with that content. Below the shared
``max_chars`` threshold, callers keep the content inline unchanged. Above it,
this module atomically writes the *exact complete* content to a workdir-
relative file under the canonical ``<agent-workdir>/tmp/tool-results/``
directory — the same directory the generic preventive spill
(``kernel/tool_result_artifacts.spill_oversized_result``) already owns and
writes to — and returns a compact artifact envelope with no content preview —
never a lossy prefix.

This module owns the spill *decision* and envelope *shape* for the ``web``
capability specifically (its own product-facing metadata, instruction
wording, and threshold policy). It reuses the kernel's shared atomic-write
primitive (``kernel/tool_result_artifacts.write_artifact_file``) and the
kernel's canonical ``WorkdirLayout.tool_results_dir`` rather than
re-implementing atomic file writes or inventing a second output directory —
there is exactly one artifact directory and one atomic-write primitive,
shared by both the generic preventive spill and this web-owned spill. The
envelope is stamped with ``artifact == WEB_ARTIFACT_MARKER`` so the kernel's
own ``is_spill_manifest`` recognizes it explicitly (see
``tool_result_artifacts.py``), which is what stops the generic preventive
spill from re-spilling an already-built web artifact — not an incidental
"the envelope happens to be small" assumption. This module does not import
from ``services/websearch`` or vice versa, keeping the browser/search
boundary documented in ``browser/CONTRACT.md`` intact.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from lingtai.kernel._fsutil import utc_now_iso
from lingtai.kernel.tool_result_artifacts import WEB_ARTIFACT_MARKER, write_artifact_file
from lingtai.kernel.workdir import workdir_layout

from .settings import OutputSettingsSnapshot


def spill_if_over_threshold(
    *,
    content: str,
    output_setting: OutputSettingsSnapshot,
    working_dir: Path,
    action: str,
    content_scope: str,
    content_kind: str,
    format: str,
    decision_chars: int | None = None,
    decision_basis: str = "content",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return ``None`` if the content fits inline, else a complete-artifact envelope.

    ``content`` is the exact canonical serialization that gets written to the
    artifact file when spilled (UTF-8 JSON for search's rendered result list,
    model-readable text for browse's joined block text). Its Unicode
    character count (``len(content)``) is measured directly for
    ``content_chars``/``content_sha256`` — this is exactly what gets written
    to disk, so those fields are trivially verifiable against the artifact
    file, regardless of what triggered the spill decision.

    ``decision_chars`` is the character count the inline-vs-artifact
    *threshold comparison* is actually made against. It defaults to
    ``len(content)`` (search's own case: the rendered JSON result list is
    both the decision content and the file content). Browse passes a
    different, larger value here — the exact canonical serialization of the
    **complete structured content that would actually be returned inline**
    (the JSON-serialized `blocks` array), which can be substantially larger
    than the joined plain-text file this module writes, because JSON
    structure/field overhead accumulates per block. Deciding on
    ``len(content)`` alone would let a canonically-large inline response
    (many small blocks) slip past the threshold undetected, exactly the bug
    this parameter exists to close. When ``decision_chars`` differs from
    ``len(content)``, the returned artifact envelope truthfully reports both:
    ``content_chars``/``content_sha256`` still describe the file that was
    written, while ``delivery_decision_chars``/``delivery_decision_basis``
    record what the threshold was actually measured against, so a spilled
    45,000-character *file* is never misreported as itself having exceeded a
    50,000-character threshold when the real trigger was a ~397,570-character
    structured inline serialization.

    ``output_setting`` is the resolved shared-setting snapshot (or its
    per-call override) already used to pick the threshold; its
    ``source``/``revision``/``digest`` are echoed onto the artifact envelope
    so a spilled result stays fully self-describing about *which* setting
    state produced this threshold, not only the numeric ``max_chars`` value.

    On a successful write the envelope never includes the content itself (no
    preview, no truncated prefix) — only the workdir-relative ``file_path``,
    exact character count, SHA-256, format/encoding, and a model-facing
    instruction to read the file in full. On write failure, returns an
    envelope with ``status: "failed"`` and ``error_code:
    "ARTIFACT_WRITE_FAILED"`` — content is never dropped silently and never
    falls back to a lossy inline truncation.
    """
    max_chars = output_setting.max_chars
    assert max_chars is not None
    content_chars = len(content)
    effective_decision_chars = content_chars if decision_chars is None else decision_chars
    if effective_decision_chars <= max_chars:
        return None

    wd = Path(working_dir)
    layout = workdir_layout(wd)
    ext = "json" if format == "json" else "txt"
    relative_path, _absolute_path, error = write_artifact_file(
        content,
        directory=layout.tool_results_dir,
        working_dir=wd,
        stem_slug=f"web-{action}",
        ext=ext,
    )
    if error is not None or relative_path is None:
        return {
            "status": "failed",
            "error_code": "ARTIFACT_WRITE_FAILED",
            "message": (
                "The complete result exceeded the inline delivery threshold "
                "and could not be written to a readable artifact file. No "
                "content was returned inline to avoid a lossy truncation."
            ),
            "content_chars": content_chars,
            "max_chars": max_chars,
            **(extra or {}),
        }

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    envelope: dict[str, Any] = {
        "delivery": "artifact",
        "artifact": WEB_ARTIFACT_MARKER,
        "content_scope": content_scope,
        "content_kind": content_kind,
        "format": format,
        "encoding": "utf-8",
        "file_path": relative_path,
        "content_chars": content_chars,
        "content_sha256": digest,
        "max_chars": max_chars,
        "output_setting_source": output_setting.source,
        "output_setting_revision": output_setting.revision,
        "output_setting_hash": output_setting.digest,
        "created_at": utc_now_iso(),
        "artifact_lifetime": "ephemeral",
        "artifact_state": "available",
        "instruction": (
            f"The complete {content_kind} ({content_chars} characters) exceeded "
            f"the {max_chars}-character inline delivery threshold and was saved "
            "in full, with no truncation, to the file at file_path. Use shell "
            "(for example bounded sed/head/rg) to read it in chunks. This artifact "
            "contains the complete canonical result; no content was omitted "
            "or shortened."
        ),
    }
    if effective_decision_chars != content_chars:
        envelope["delivery_decision_chars"] = effective_decision_chars
        envelope["delivery_decision_basis"] = decision_basis
    if extra:
        envelope.update(extra)
    return envelope
