"""agent m001 — retire init.json procedures overrides.

``procedures.md`` is kernel-owned. Earlier agents could set
``init.json.procedures`` (inline prompt text) or ``init.json.procedures_file``
(file path override). This agent-domain migration preserves non-empty inline
legacy content through the workspace archive and removes both fields from
``init.json``.

Idempotency is provided by the versioned migration runner: this migration is
run at most once per agent workdir version. Within the migration itself, missing
fields are a no-op.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .migrate import MigrationWorkspacePort


def migrate_init_procedures_override(workspace: MigrationWorkspacePort) -> None:
    """Archive/remove legacy procedures fields from an agent workdir init.json."""
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

    has_inline = "procedures" in data
    has_file = "procedures_file" in data
    if not has_inline and not has_file:
        return

    legacy = data.get("procedures")
    archive_rel: str | None = None
    content_hash: str | None = None
    byte_length = 0
    char_length = 0

    if isinstance(legacy, str) and legacy != "":
        try:
            result = workspace.archive(MigrationArchiveKind.INIT_PROCEDURES, legacy)
        except MigrationWorkspaceError as e:
            workspace.append_audit(
                "init_procedures_override_migration_failed",
                {"reason": str(e)},
            )
            raise
        archive_rel = result.relative_path
        content_hash = result.content_hash
        byte_length = result.byte_length
        char_length = result.char_length

    data.pop("procedures", None)
    data.pop("procedures_file", None)

    try:
        workspace.atomic_replace_entry(
            INIT_DOCUMENT_REF,
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        )
    except MigrationWorkspaceError as e:
        workspace.append_audit(
            "init_procedures_override_migration_failed",
            {
                "reason": str(e),
                "archive_path": archive_rel,
                "content_hash": content_hash,
                "byte_length": byte_length,
                "char_length": char_length,
            },
        )
        raise

    workspace.append_audit(
        "init_procedures_override_migrated",
        {
            "archive_path": archive_rel,
            "content_hash": content_hash,
            "byte_length": byte_length,
            "char_length": char_length,
            "procedures_removed": has_inline,
            "procedures_file_removed": has_file,
            "field_removed": True,
        },
    )
