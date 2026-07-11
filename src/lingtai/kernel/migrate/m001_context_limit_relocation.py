"""m001 — relocate manifest.context_limit into manifest.llm.context_limit.

context_limit is a property of the model and belongs inside the llm block.
This migration walks every preset file in the library and rewrites in
place when the old layout is detected.

Idempotent: a preset that already has context_limit inside llm (or no
context_limit at all) is left untouched. A preset that somehow has both
is also left untouched and warned about — `load_preset`'s validator will
reject it on read, surfacing the conflict to the user without our
migration silently picking a side.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from lingtai.kernel.config_resolve import load_jsonc

log = logging.getLogger(__name__)

_PRESET_SUFFIXES = (".json", ".jsonc")


def migrate_context_limit_relocation(presets_path: Path) -> None:
    """Walk preset files and move context_limit from manifest root → manifest.llm.

    Args:
        presets_path: directory containing the preset files. Must exist
            (the caller checks).

    Side effects:
        Rewrites preset files in place atomically (tmp + os.replace).
        Logs each rewrite at INFO level. Logs warnings for skipped files
        (parse errors, ambiguous layout, etc.) and continues.
    """
    rewrote = 0
    skipped = 0

    for entry in sorted(presets_path.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix not in _PRESET_SUFFIXES:
            continue
        if entry.name.startswith("_"):
            continue  # internal files like _kernel_meta.json

        try:
            data = load_jsonc(entry)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("m001: skipping unreadable preset %s: %s", entry, e)
            skipped += 1
            continue

        if not isinstance(data, dict):
            continue

        manifest = data.get("manifest")
        if not isinstance(manifest, dict):
            continue

        # Old-layout signal: context_limit at manifest root.
        if "context_limit" not in manifest:
            continue

        llm = manifest.get("llm")
        if not isinstance(llm, dict):
            # Malformed preset — load_preset will reject it later. Don't
            # touch.
            log.warning(
                "m001: %s has manifest.context_limit but no llm dict — leaving unchanged",
                entry,
            )
            skipped += 1
            continue

        if "context_limit" in llm:
            # Both locations populated — ambiguous. load_preset's validator
            # will reject this on read. Don't silently pick a side.
            log.warning(
                "m001: %s has context_limit in both manifest root and manifest.llm — leaving unchanged",
                entry,
            )
            skipped += 1
            continue

        # Move it.
        llm["context_limit"] = manifest.pop("context_limit")

        try:
            tmp = entry.with_suffix(entry.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(entry))
        except OSError as e:
            log.warning("m001: failed to rewrite %s: %s", entry, e)
            skipped += 1
            continue

        log.info("m001: relocated context_limit in %s", entry.name)
        rewrote += 1

    log.info(
        "m001 complete: rewrote=%d skipped=%d (presets_path=%s)",
        rewrote, skipped, presets_path,
    )
