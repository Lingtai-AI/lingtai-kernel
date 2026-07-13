"""agent m003 — retire init.json substrate/brief prompt overrides.

The init-prompt contract narrows the externally changeable system-prompt
surface to exactly ``base_prompt``, ``covenant``, and ``comment``. Two prompt
sections that used to accept init.json overrides are retired here:

- ``substrate`` — kernel-owned architecture model. The packaged
  ``lingtai/prompts/substrate/substrate.md`` is now the sole source.
- ``brief`` — secretary-written life context. Now sourced solely from
  ``system/brief.md`` on disk, not init.json.

This agent-domain migration preserves non-empty legacy ``substrate`` content
through the workspace archive and removes the inline + ``_file`` fields from
``init.json`` for both. ``brief`` is already deprecated and is intentionally
ignored: the migration removes ``brief`` / ``brief_file`` without seeding prompt
content. Active brief context must live in ``system/brief.md``.

Idempotency is provided by the versioned migration runner: this migration runs
at most once per agent workdir version. Within the migration itself, missing
fields are a no-op. Mirrors ``agent_m001_init_procedures_override``.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .migrate import MigrationWorkspacePort


def migrate_init_prompt_contract(workspace: MigrationWorkspacePort) -> None:
    """Archive/remove legacy substrate/brief overrides from an agent init.json."""
    from .migrate import (
        INIT_DOCUMENT_REF,
        MigrationArchiveKind,
        MigrationWorkspaceError,
    )

    text = workspace.read_entry(INIT_DOCUMENT_REF)
    if text is None:
        return
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("init.json did not contain a JSON object")

    touched: dict[str, dict] = {}

    for field in ("substrate", "brief"):
        file_key = f"{field}_file"
        has_inline = field in data
        has_file = file_key in data
        if not has_inline and not has_file:
            continue

        inline = data.get(field)
        archive_rel: str | None = None
        archive_source: str | None = None
        if field == "substrate" and isinstance(inline, str) and inline != "":
            try:
                result = workspace.archive(MigrationArchiveKind.INIT_SUBSTRATE, inline)
            except MigrationWorkspaceError as e:
                workspace.append_audit(
                    "init_prompt_contract_migration_failed",
                    {"field": field, "reason": str(e)},
                )
                raise
            archive_rel = result.relative_path
            archive_source = "inline"

        data.pop(field, None)
        data.pop(file_key, None)
        touched[field] = {
            "archive_path": archive_rel,
            "archive_source": archive_source,
            "inline_removed": has_inline,
            "file_removed": has_file,
            "ignored_deprecated": field == "brief",
        }

    if not touched:
        return

    try:
        workspace.atomic_replace_entry(
            INIT_DOCUMENT_REF,
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        )
    except MigrationWorkspaceError as e:
        workspace.append_audit(
            "init_prompt_contract_migration_failed",
            {"reason": str(e), "touched": touched},
        )
        raise

    workspace.append_audit(
        "init_prompt_contract_migrated",
        {"touched": touched, "field_removed": True},
    )
