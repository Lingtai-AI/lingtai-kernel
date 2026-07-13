"""m001 — relocate manifest.context_limit into manifest.llm.context_limit.

context_limit is a property of the model and belongs inside the llm block.
This migration walks every preset document the workspace enumerates and
rewrites in place when the old layout is detected.

Idempotent: a preset that already has context_limit inside llm (or no
context_limit at all) is left untouched. A preset that somehow has both
is also left untouched and warned about — `load_preset`'s validator will
reject it on read, surfacing the conflict to the user without our
migration silently picking a side.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from lingtai.kernel.config_resolve import parse_jsonc

if TYPE_CHECKING:
    from .migrate import MigrationWorkspacePort

log = logging.getLogger(__name__)


def migrate_context_limit_relocation(workspace: MigrationWorkspacePort) -> None:
    """Walk preset documents and move context_limit from manifest root → manifest.llm.

    Side effects:
        Rewrites preset documents through the workspace (atomic replace owned by
        the adapter). Logs each rewrite at INFO level. Logs warnings for skipped
        documents (parse errors, ambiguous layout, etc.) and continues.
    """
    from .migrate import MigrationWorkspaceError

    rewrote = 0
    skipped = 0

    for ref in workspace.enumerate_entries():
        text = workspace.read_entry(ref)
        if text is None:
            log.warning("m001: skipping unreadable preset %s", ref.name)
            skipped += 1
            continue

        try:
            data = parse_jsonc(text)
        except json.JSONDecodeError as e:
            log.warning("m001: skipping unreadable preset %s: %s", ref.name, e)
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
            # Malformed preset — load_preset will reject it later. Don't touch.
            log.warning(
                "m001: %s has manifest.context_limit but no llm dict — leaving unchanged",
                ref.name,
            )
            skipped += 1
            continue

        if "context_limit" in llm:
            # Both locations populated — ambiguous. load_preset's validator
            # will reject this on read. Don't silently pick a side.
            log.warning(
                "m001: %s has context_limit in both manifest root and manifest.llm — leaving unchanged",
                ref.name,
            )
            skipped += 1
            continue

        # Move it.
        llm["context_limit"] = manifest.pop("context_limit")

        try:
            workspace.atomic_replace_entry(
                ref, json.dumps(data, indent=2, ensure_ascii=False)
            )
        except MigrationWorkspaceError as e:
            log.warning("m001: failed to rewrite %s: %s", ref.name, e)
            skipped += 1
            continue

        log.info("m001: relocated context_limit in %s", ref.name)
        rewrote += 1

    log.info("m001 complete: rewrote=%d skipped=%d", rewrote, skipped)
